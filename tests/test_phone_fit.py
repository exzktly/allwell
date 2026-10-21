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
    QApplication, QBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
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


def test_every_datasets_tool_fits_a_phone(datasets_pane):
    """The nine tools as phone layout draws them, in a 400 px-wide page. A
    tool added tomorrow is covered by being in the pane."""
    tab = datasets_pane
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
    assert not too_wide, f"Datasets tools still wider than a phone: {too_wide}"
