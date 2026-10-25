"""Phone layout: a desk-shaped form fits a phone's width (``ui/phone_fit``).

Reported: in phone layout the Datasets tools were "basically unusable".
Measured on a 432×936 phone-shaped screen, seven of the nine were wider than the
phone, with controls up to 479 px past its right edge: paired fields side by
side, option rows, a dashboard of eight counters in one ``QHBoxLayout``, and
radio buttons whose labels are sentences — none of which can wrap.

``phone_fit.fit`` stacks the rows that overflow (outermost first) and breaks
the labels that still do not fit; ``unfit`` gives the desk form back.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from ui import phone_fit


def _field(label: str) -> QWidget:
    """A labelled path field with its browse button beside it, as the
    Datasets tools build one."""
    col = QWidget()
    v = QVBoxLayout(col)
    v.setContentsMargins(0, 0, 0, 0)
    v.addWidget(QLabel(label))
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    edit = QLineEdit()
    edit.setMinimumWidth(160)
    h.addWidget(edit)
    h.addWidget(QToolButton())
    v.addWidget(row)
    return col


def _form(qtbot, width: int):
    area = QScrollArea()
    qtbot.addWidget(area)
    area.setWidgetResizable(True)
    content = QWidget()
    lay = QVBoxLayout(content)
    pair = QWidget()
    ph = QHBoxLayout(pair)
    ph.setContentsMargins(0, 0, 0, 0)
    one, two = _field("Dataset 1"), _field("Dataset 2")
    ph.addWidget(one)
    ph.addWidget(QLabel("+"))
    ph.addWidget(two)
    lay.addWidget(pair)
    radio = QRadioButton("Create a new dataset in the destination folder, leaving both originals")
    lay.addWidget(radio)
    buttons = QWidget()
    bh = QHBoxLayout(buttons)
    bh.addWidget(QPushButton("Concatenate"))
    bh.addWidget(QPushButton("+ Queue"))
    lay.addWidget(buttons)
    area.setWidget(content)
    area.resize(width, 600)
    area.show()
    qtbot.waitExposed(area)
    return area, pair, one, radio, buttons


def _min_width(w) -> int:
    return max(w.minimumWidth(), w.minimumSizeHint().width())


def test_a_form_wider_than_the_phone_is_narrowed_to_it(qtbot):
    area, pair, one, radio, buttons = _form(qtbot, 300)
    assert _min_width(area.widget()) > area.viewport().width(), \
        "the fixture must start wider than its viewport, or it measures nothing"
    assert phone_fit.fit(area)
    assert _min_width(area.widget()) <= area.viewport().width()
    # The pair stacked; each field kept its browse button beside it, and a
    # row that fitted was left alone.
    assert pair.layout().direction() == QBoxLayout.TopToBottom
    inner = one.findChildren(QWidget)[-1].parentWidget().layout()
    assert inner.direction() == QBoxLayout.LeftToRight
    assert buttons.layout().direction() == QBoxLayout.LeftToRight
    assert "\n" in radio.text(), "the long radio label was not broken"


def test_unfit_gives_the_desk_form_back(qtbot):
    area, pair, _one, radio, _buttons = _form(qtbot, 300)
    text = radio.text()
    phone_fit.fit(area)
    phone_fit.unfit(area)
    assert pair.layout().direction() == QBoxLayout.LeftToRight
    assert radio.text() == text


def test_a_form_that_fits_is_left_alone(qtbot):
    area, pair, _one, radio, _buttons = _form(qtbot, 1200)
    text = radio.text()
    assert phone_fit.fit(area)
    assert pair.layout().direction() == QBoxLayout.LeftToRight
    assert radio.text() == text


def test_a_heading_that_does_not_wrap_is_set_to_wrap(qtbot):
    """A ``QLabel`` reports its whole text as its minimum width unless it
    wraps; a heading longer than the page held it wide."""
    area, *_ = _form(qtbot, 300)
    heading = QLabel("How are the filenames laid out in this folder of images, "
                     "and which part of each name says the well?")
    area.widget().layout().insertWidget(0, heading)
    QApplication.processEvents()     # a widget added to a shown form shows on the next pass
    assert heading.isVisible()
    assert _min_width(heading) > area.viewport().width(), \
        "the heading must start wider than the page, or this measures nothing"
    assert phone_fit.fit(area)
    assert heading.wordWrap()
    phone_fit.unfit(area)
    assert not heading.wordWrap()


@pytest.mark.parametrize("scale,touch", [(1.0, None), (1.6, 1.1)])
def test_a_broken_label_is_measured_line_by_line_under_the_app_s_type(
        qtbot, qapp, scale, touch):
    """Under the app's stylesheet at a large type scale, a label broken by an
    average character width came out with lines still wider than the page,
    and was never broken again: CI, whose DejaVu Sans runs wider than Inter,
    failed the synthetic form here with ``fit`` returning False."""
    from ui import theme
    before = qapp.styleSheet()
    theme.set_touch_unit(touch)
    theme.apply_ui_scale(scale)
    qapp.setStyleSheet(theme.qss())
    try:
        area, _pair, _one, radio, _buttons = _form(qtbot, 300)
        assert phone_fit.fit(area), (
            f"{_min_width(area.widget())} px in {area.viewport().width()} px")
        fm = radio.fontMetrics()
        widest = max(fm.horizontalAdvance(line) for line in radio.text().split("\n"))
        assert widest < area.viewport().width()
    finally:
        theme.set_touch_unit(None)
        theme.apply_ui_scale(1.0)
        qapp.setStyleSheet(before)


def test_a_combo_sized_to_its_longest_entry_is_let_shrink(qtbot):
    """A combo box reports its longest entry as its minimum width; one held
    Convert ND2 three pixels wider than the page in DejaVu Sans."""
    area, *_ = _form(qtbot, 300)
    combo = QComboBox()
    combo.addItems(["Short", "The file's own position names, read from its "
                             "metadata and matched to the plate"])
    area.widget().layout().insertWidget(0, combo)
    QApplication.processEvents()
    assert _min_width(combo) > area.viewport().width(), \
        "the combo must start wider than the page, or this measures nothing"
    assert phone_fit.fit(area)
    assert _min_width(combo) <= area.viewport().width()
    phone_fit.unfit(area)
    assert combo.sizeAdjustPolicy() == QComboBox.AdjustToContentsOnFirstShow
    assert _min_width(combo) > area.viewport().width()


def test_a_widget_with_its_own_phone_form_takes_it(qtbot):
    """A dashboard built on ``PlateProgress`` — the CZI and ND2 converters',
    Concatenate's, Analyze's — knows its phone form (``setStacked``: the stage
    rail above the plate, the legend and dividers hidden). Overflowing, it is
    given that form rather than the generic row stacking, and the undo gives
    the desk form back."""
    from widgets.run_dashboard import RunDashboard

    area = QScrollArea()
    qtbot.addWidget(area)
    area.setWidgetResizable(True)
    dash = RunDashboard()
    area.setWidget(dash)
    area.resize(300, 800)
    area.show()
    qtbot.waitExposed(area)
    assert _min_width(dash) > area.viewport().width(), \
        "the dashboard must start wider than the page, or this measures nothing"
    assert not dash._stacked

    assert phone_fit.fit(area)
    assert dash._stacked, "the dashboard was not given its own phone form"
    phone_fit.unfit(area)
    assert not dash._stacked


# ── every Datasets tool, at a phone's width and touch size ─────────────────

@pytest.fixture(scope="module")
def datasets_pane(qapp, tmp_path_factory):
    """The Datasets pane at phone layout's touch and type scale, built once.

    Module-scoped so the fast lane's widget reaper leaves it alone: force-
    deleting a shown ``DatasetsTab`` (``shiboken6.delete``) crashed inside
    Shiboken's wrapper release about three runs in four — with or without
    any fitting, measured under gdb — while the app's own quit, with every
    page fitted, exits cleanly.
    """
    from datasets.tab import DatasetsTab
    from PySide6.QtWidgets import QSplitter
    from ui import theme

    before = qapp.styleSheet()
    theme.set_touch_unit(1.1)
    theme.apply_ui_scale(1.3)
    qapp.setStyleSheet(theme.qss())
    tab = DatasetsTab(queue_state_dir=tmp_path_factory.mktemp("datasets_queue"))
    # In phone layout the tool list is in ☰, not beside the page.
    tab.findChild(QSplitter).widget(0).hide()
    tab.resize(400, 900)
    tab.show()
    QApplication.processEvents()
    yield tab
    tab.shutdown()
    tab.hide()
    theme.set_touch_unit(None)
    theme.apply_ui_scale(1.0)
    qapp.setStyleSheet(before)


def _tools_too_wide(tab) -> dict:
    names = tab.tool_names()
    assert len(names) >= 9
    too_wide, fitted = {}, []
    try:
        for name in names:
            tab.show_tool(name)
            QApplication.processEvents()
            page = tab._pages.currentWidget()
            areas = [a for a in page.findChildren(QScrollArea)
                     if a.isVisible() and a.widget() is not None]
            if not areas:
                continue
            area = max(areas, key=lambda a: a.widget().height())
            phone_fit.fit(area)
            fitted.append(area)
            need, have = _min_width(area.widget()), area.viewport().width()
            if need > have:
                too_wide[name] = f"{need} px in {have} px"
    finally:
        for area in fitted:
            phone_fit.unfit(area)
    return too_wide


def test_every_datasets_tool_fits_a_phone(datasets_pane):
    """The nine tools as phone layout draws them, in a 400 px-wide page. A
    tool added tomorrow is covered by being in the pane."""
    too_wide = _tools_too_wide(datasets_pane)
    assert not too_wide, f"Datasets tools still wider than a phone: {too_wide}"


def test_every_datasets_tool_fits_with_wider_type(qapp, datasets_pane):
    """CI's runner set Zip Loose Folder one pixel wider than the page
    (390 px in 389) where this machine fitted it with 18 to spare: its fonts
    run wider. The same tools at a larger type scale leave no such slack, so
    a page held wide by text that cannot wrap fails here on any machine."""
    from ui import theme
    before = theme.ui_scale()
    theme.apply_ui_scale(1.6)
    qapp.setStyleSheet(theme.qss())
    try:
        too_wide = _tools_too_wide(datasets_pane)
    finally:
        theme.apply_ui_scale(before)
        qapp.setStyleSheet(theme.qss())
    assert not too_wide, f"Datasets tools wider than a phone at large type: {too_wide}"


# ── what Home's Overview needed from the fit ────────────────────────────────


def test_a_name_over_a_folder_is_broken_line_by_line(qtbot):
    """A recent-dataset row is a name over a folder, two lines the author wrote.
    Each is broken on its own — after a separator, an underscore or a hyphen,
    since neither has spaces — and the author's line break is kept."""
    from PySide6.QtWidgets import QPushButton

    from ui.phone_fit import _break_label

    b = QPushButton("2026-10-21_HeLa_DAPI_plate07\nC:\\Users\\lab\\Microscopy\\October")
    qtbot.addWidget(b)
    assert _break_label(b, 120)
    lines = b.text().split("\n")
    assert lines[0].startswith("2026-") and lines[-1].endswith("October")
    assert "".join(lines) == "2026-10-21_HeLa_DAPI_plate07C:\\Users\\lab\\Microscopy\\October", (
        "breaking lost or reordered characters")
    assert len(lines) > 2, "the two author lines were not broken any further"


def test_unfit_does_not_put_back_text_the_page_has_replaced(qtbot):
    """Home repaints its labels from the record. A fitted button whose page has
    since set a new label must keep the new one when the fit is undone —
    restoring the original would show the previous dataset's text."""
    from PySide6.QtWidgets import QPushButton, QScrollArea, QVBoxLayout, QWidget

    from ui.phone_fit import _break_label, unfit

    area = QScrollArea()
    qtbot.addWidget(area)
    content = QWidget()
    QVBoxLayout(content)
    b = QPushButton("first label that is long enough to be broken here")
    content.layout().addWidget(b)
    area.setWidget(content)

    assert _break_label(b, 80), "premise: the label was broken"
    b.setText("second label")              # the page repaints
    unfit(area)
    assert b.text() == "second label"


def test_a_row_built_after_the_page_showed_is_fitted_at_once(qtbot):
    """Qt shows a widget added to an already-visible layout on the next turn;
    a fit straight after building it must still see it. One that is a window,
    or hidden on purpose, is not shown."""
    from PySide6.QtWidgets import (
        QDialog, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
    )

    from ui.phone_fit import fit

    area = QScrollArea()
    area.setWidgetResizable(True)
    qtbot.addWidget(area)
    content = QWidget()
    QVBoxLayout(content)
    area.setWidget(content)
    area.resize(320, 400)
    area.show()
    qtbot.waitExposed(area)

    row = QWidget()
    h = QHBoxLayout(row)
    for _ in range(3):
        e = QLineEdit()
        e.setMinimumWidth(160)
        h.addWidget(e)
    content.layout().addWidget(row)          # shown by Qt only next turn
    hidden = QWidget()
    content.layout().addWidget(hidden)
    hidden.hide()
    dialog = QDialog(content)

    assert fit(area)
    assert h.direction() == QHBoxLayout.TopToBottom, (
        "the row built after the page showed was not seen by the fit")
    assert hidden.isHidden() and not dialog.isVisible()
