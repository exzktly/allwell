"""A control band laid out as a form — phone layout's Controls sheet.

Reported: the Controls sheet was "a mess". Its controls flowed left to right at
their own widths, so rows wrapped wherever a label happened to end and there
was no edge to read down; some controls carried their label inside their text
("Channel: GFP", "Timepoints: 1/3 selected"), the threshold had a small grey
label beside it, others had none; a hint floated between controls; data
choices, display options and commands were interleaved. ``ControlBand.setForm``
lays the same widgets out as one column: Data rows of label + control, then
Display and Actions as grids of equal-width buttons (``widgets/control_band_form.py``).

The band here is built the way the Bar Plots page builds its own — the same
kinds of control, added through the same calls — so a form that handles only
one kind of label (the bundled caption, say) leaves the others in their text
and fails here.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QLabel, QPushButton, QSpinBox, QToolButton, QWidget,
)

from widgets.control_band import ControlBand
from widgets.control_band_form import SECTIONS, section_of_action, split_labelled_text
from widgets.icon_button import IconButton
from widgets.labeled_combo import LabeledComboBox

LABEL_W, ROW_H, GAP = 111, 53, 9


def _band(qtbot):
    band = ControlBand()
    qtbot.addWidget(band)
    combo = QComboBox()
    combo.addItems(["GFP", "mCherry"])
    prop = LabeledComboBox("Property")
    prop.addItems(["Mean Intensity", "Area"])
    tp = QPushButton("Timepoints: 1/3 selected")
    hint = QLabel("Plots draw one member per selected well.")
    hint.setObjectName("Muted")
    tp.setFixedWidth(150)          # pinned, like a touch stepper
    thr = QSpinBox()
    thr.setFixedWidth(90)
    parts = {
        "channel": band.add_scope("Channel", combo),
        "property": band.add_scope("", prop),
        "tp": band.add_scope("", tp),
        "hint": band.add_scope("", hint),
        "threshold": band.add_scope("ON threshold", thr, trailing=True),
    }
    # Two panel rows — one that reads a value, one that has none — and the
    # root of the Display tab, which is not in the form at all.
    fold = QToolButton()
    fold.setText("Fold change: vs DMSO ›")
    fold.setProperty("bandPanel", "row")
    fit = QToolButton()
    fit.setText("Fit ›")
    fit.setProperty("bandPanel", "row")
    display = QToolButton()
    display.setText("Display")
    display.setProperty("bandPanel", "root")
    reset = QPushButton("Reset Order")
    save = QToolButton()
    save.setText("Save ▾")
    save.setPopupMode(QToolButton.InstantPopup)
    save.setProperty("bandRole", "action")        # a menu, but of commands
    props = IconButton("sliders-horizontal", tooltip="Show / hide the figure properties panel")
    props.setProperty("sheetText", "Properties")
    for w in (fold, fit, display, reset, save, props):
        band.add_action(w)
    band.resize(400, 900)
    band.show()
    qtbot.waitExposed(band)
    widgets = dict(combo=combo, prop=prop, tp=tp, hint=hint, thr=thr, fold=fold, fit=fit,
                   display=display, reset=reset, save=save, props=props)
    return band, parts, widgets


def _settle():
    for _ in range(3):
        QApplication.processEvents()


def _row_label(row) -> str:
    labels = [w for w in row.findChildren(QLabel) if w.parentWidget() is row]
    return labels[0].text() if labels else ""


def test_split_labelled_text():
    assert split_labelled_text("Timepoints: 1/3 selected") == ("Timepoints", "1/3 selected")
    assert split_labelled_text("Series: None") == ("Series", "None")
    assert split_labelled_text("Display ▾") is None
    assert split_labelled_text("Reset Order") is None


def test_every_scope_control_is_a_row_with_its_label_in_one_column(qtbot):
    """Each control is one row: its label in the left column — wherever the
    page put that label — and the control filling the rest, every control's
    left and right edge the same. The wrong implementations this rejects: a
    form that stacks the controls but leaves "Channel:" and "Timepoints:"
    inside them (labels column empty for those rows), and one that keeps
    their own widths (ragged right edges)."""
    band, _parts, w = _band(qtbot)
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    _settle()
    form = band.form()
    rows = form.rows("Data")
    # The two panel rows are scope: they sit with the other choices of data,
    # before the trailing threshold, not in the action zone.
    assert [_row_label(r) for r, _i in rows] == [
        "Channel", "Property", "Timepoints", "Fold change", "Fit", "ON threshold"]
    for row, item in rows:
        assert row.minimumHeight() == ROW_H
        assert [lb.width() for lb in row.findChildren(QLabel) if lb.isVisible()
                and lb.parentWidget() is row] == [LABEL_W]
    lefts = {item.mapTo(band, item.rect().topLeft()).x() for _r, item in rows}
    rights = {item.mapTo(band, item.rect().topRight()).x() for _r, item in rows}
    assert len(lefts) == 1 and len(rights) == 1, (lefts, rights)
    # The label left the control: the combo paints only its value, the
    # button's own text is untouched (pages rewrite it) but painted as value.
    assert w["prop"]._painted_text() == "Mean Intensity"
    assert w["tp"].text() == "Timepoints: 1/3 selected"
    # The hint is a caption at the top of Data, not a row of its own.
    assert form.hints() == [w["hint"]] and w["hint"].wordWrap()


def test_a_labelled_button_is_painted_as_its_value_only(qtbot):
    """Assert what reaches the screen: the Timepoints button in the form draws
    exactly what a button reading "1/3 selected" draws — and keeps doing so
    after the page rewrites its text."""
    band, _parts, w = _band(qtbot)
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    _settle()
    tp = w["tp"]

    def twin_of(text):
        twin = QPushButton(text)
        qtbot.addWidget(twin)
        twin.setProperty("sheetValue", True)
        twin.setParent(tp.parentWidget())
        twin.setGeometry(tp.geometry())
        twin.setStyleSheet(tp.styleSheet())
        twin.show()
        _settle()
        return twin.grab().toImage()

    assert tp.grab().toImage() == twin_of("1/3 selected")
    assert tp.grab().toImage() != twin_of("Timepoints: 1/3 selected")
    tp.setText("Timepoints: 2/3 selected")
    _settle()
    assert tp.grab().toImage() == twin_of("2/3 selected")


def test_the_form_is_the_data_tab_panel_rows_are_rows_and_a_tab_s_root_is_not_in_it(qtbot):
    """The form is what the inspector's Data tab holds. A button that opens a
    page is a row like any other — its title in the label column, its value in
    the control — and the root of another tab (Display) is in no row and no
    grid: its panel is the tab beside this one.

    Wrong implementations this rejects: the old Display grid (a button for the
    tab next door), a panel row left in the Actions grid, and a row that still
    reads its own title beside the label column."""
    band, _parts, w = _band(qtbot)
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    _settle()
    form = band.form()
    assert SECTIONS == ("Actions", "Data")
    y = {n: form.section_box(n).y() for n in SECTIONS}
    assert y["Actions"] < y["Data"], y
    assert form.buttons("Actions") == [w["reset"], w["save"], w["props"]]
    assert form.parked() == [w["display"]]
    assert not w["display"].isVisible(), "the Display tab's button is shown beside the tab"
    assert not w["display"].isHidden(), "parking must keep the visibility the page gave it"
    row_items = [i for _r, i in form.rows("Data")]
    assert w["fold"] in row_items and w["fit"] in row_items
    # The value alone is painted; the text the page set is untouched.
    painter = {type(o).__name__: o for o in w["fold"].children()}["_ValueOnly"]
    assert painter.value(w["fold"].text()) == "vs DMSO ›"
    assert w["fold"].text() == "Fold change: vs DMSO ›"
    bare = {type(o).__name__: o for o in w["fit"].children()}["_ValueOnly"]
    assert bare.value(w["fit"].text()) == "›"
    # The tabs above the form say "Data"; the form does not say it again.
    assert not form._sections["Data"]["title"].isVisible()
    # Equal cells: Qt hands the odd pixel of a division to one of them.
    widths = [b.width() for b in form.buttons("Actions")]
    assert max(widths) - min(widths) <= 1, widths
    # An icon-only button says what it is.
    assert w["props"].text() == "Properties"


def test_a_disabled_action_goes_after_the_live_ones(qtbot):
    band, _parts, w = _band(qtbot)
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    _settle()
    grid = band.form().section_box("Actions").layout().itemAt(2).layout()

    def cells():
        return [grid.getItemPosition(grid.indexOf(b))[:2]
                for b in (w["reset"], w["save"], w["props"])]
    assert cells() == [(0, 0), (0, 1), (0, 2)]
    w["reset"].setEnabled(False)
    _settle()
    assert cells() == [(0, 2), (0, 0), (0, 1)], "a disabled button stayed between live ones"


def test_the_page_still_owns_visibility(qtbot):
    """A row, a cell and a section follow what the page shows: a leaf's
    controls hidden by ``show_slot`` must not leave their labels behind."""
    band, parts, w = _band(qtbot)
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    _settle()
    form = band.form()
    row_of = {id(i): r for r, i in form.rows("Data")}
    parts["threshold"].hide()
    _settle()
    assert not row_of[id(parts["threshold"])].isVisible()
    parts["threshold"].show()
    _settle()
    assert row_of[id(parts["threshold"])].isVisible()
    w["fold"].hide()                      # a panel row follows its button too
    _settle()
    assert not row_of[id(w["fold"])].isVisible()
    for b in (w["reset"], w["save"], w["props"]):
        b.hide()
    _settle()
    assert not form.section_box("Actions").isVisible()


def test_leaving_the_form_puts_the_row_back_as_it_was(qtbot):
    band, parts, w = _band(qtbot)
    scope_before = [band._scope.itemAt(i).widget() for i in range(band.scope_count())]
    actions_before = [band._actions.itemAt(i).widget() for i in range(band.action_count())]
    assert w["display"] in actions_before and w["fold"] in scope_before
    margins = band.layout().contentsMargins()
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    _settle()
    parts["hint"].hide()                  # the page changes something meanwhile
    band.setForm(False)
    _settle()
    assert band.form() is None
    assert [band._scope.itemAt(i).widget() for i in range(band.scope_count())] == scope_before
    assert [band._actions.itemAt(i).widget() for i in range(band.action_count())] == actions_before
    assert band.layout().contentsMargins() == margins
    assert parts["hint"].isHidden()       # the page's choice survives
    cap = parts["channel"].layout().itemAt(0).widget()
    assert isinstance(cap, QLabel) and cap.text() == "Channel" and cap.isVisible()
    assert w["prop"]._painted_text() == "Property: Mean Intensity"
    assert w["props"].text() == ""
    # No filter of the form's is left on the widgets.
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    for widget in (w["tp"], parts["channel"], w["save"]):
        assert not [o for o in widget.children()
                    if type(o).__name__ in ("_ValueOnly", "_Mirror")], widget


@pytest.mark.parametrize("panel,expect", [("root", "Parked"), ("row", "Data"), (None, "Actions")])
def test_where_an_action_goes_is_what_its_builder_declared(qtbot, panel, expect):
    b = QToolButton()
    qtbot.addWidget(b)
    b.setPopupMode(QToolButton.InstantPopup)   # dropping a menu decides nothing
    if panel:
        b.setProperty("bandPanel", panel)
    assert section_of_action(b) == expect


def test_the_shared_builders_declare_their_role(qtbot):
    """Only the builders know what a button is, so each declares it: a tab's
    root panel, a panel that is a row, a menu of commands — and the Properties
    toggle says what it is in words."""
    from types import SimpleNamespace

    from review.core.inspector_panels import make_popover_button
    from review.core.ui_helpers import make_action_menu_button, make_properties_button
    host = QWidget()
    qtbot.addWidget(host)
    root, _c, _l = make_popover_button("Display", host, root=True)
    row, _c2, _l2 = make_popover_button("Fold change", host, tab="data", summary=lambda: "vs DMSO")
    cmd, _m = make_action_menu_button("Save ▾", host)
    prop = make_properties_button(SimpleNamespace(figures=None), host, "bar")
    assert section_of_action(root) == "Parked" and root.text() == "Display"
    assert section_of_action(row) == "Data" and row.text() == "Fold change: vs DMSO ›"
    assert section_of_action(cmd) == "Actions"
    assert prop.property("sheetText") == "Style" and prop.text() == "Style"
    assert prop.property("bandRole") == "style" and cmd.property("bandRole") == "action"


def test_a_flat_icon_button_is_drawn_as_a_button_in_the_sheet(qtbot):
    """The Properties toggle is an ``IconButton``, whose own stylesheet is
    transparent: in the Actions grid it was the one cell with no fill or edge.
    In the sheet it takes the application's button style; leaving the form
    gives its own back. Another tool button's stylesheet is left alone — a
    popover button's hides its menu arrow, and clearing it drew a stray one."""
    band, _parts, w = _band(qtbot)
    own = w["props"].styleSheet()
    assert "transparent" in own
    fold_qss = "QToolButton::menu-indicator { image: none; }"
    w["fold"].setStyleSheet(fold_qss)
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    _settle()
    assert w["props"].styleSheet() == ""
    assert w["fold"].styleSheet() == fold_qss
    band.setForm(False)
    _settle()
    assert w["props"].styleSheet() == own


def test_a_form_gets_its_whole_height_in_a_column_that_does_not_size_to_it(qtbot):
    """A band of actions alone (Cell Gating's) laid out as a form in a column that does not size itself to its
    content — the docked Controls sheet on an unfolded foldable: a scroll area over a holder with a stretch under
    the band — gets its whole height, and the row's one-row floor comes back when the form ends.

    A parent layout sizes the band through its ``QWidgetItem``, which asks the band's *own* layout for a height at a
    width; as a form of actions alone that layout answers -1, and the parent fell back to the band's explicit
    minimum — the row's one row, 44 px — so the form showed a clipped ACTIONS heading and nothing under it. A band
    with data controls as well happened to measure right, which is why the Bar Plots page never showed it. Wrong
    implementations: the row's floor kept, or no floor at all (a form 0 px tall)."""
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout

    window = QWidget()
    qtbot.addWidget(window)
    QVBoxLayout(window)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    window.layout().addWidget(scroll)
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.addStretch(1)
    scroll.setWidget(holder)
    band = ControlBand()
    for text in ("Jump to the CDFs", "Reset", "Save ▾", "Export"):
        button = QPushButton(text)
        button.setProperty("bandRole", "action")
        band.add_action(button)
    row_floor = band.minimumHeight()
    window.resize(400, 1200)
    window.show()
    qtbot.waitExposed(window)
    lay.insertWidget(0, band)
    band.setForm(True, label_width=LABEL_W, row_height=ROW_H, gap=GAP)
    band.show()
    _settle()
    _settle()
    want = band._form_height(band.width())
    assert want > 2 * row_floor                     # the case measures something: the form is several rows
    assert band.height() == want, f"the form is {band.height()} px of the {want} it needs"
    band.setForm(False)
    _settle()
    assert band.minimumHeight() == row_floor == band.band_height()
