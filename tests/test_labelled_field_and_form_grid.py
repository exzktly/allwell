"""A label set outside its control (``widgets/labelled_field.py``) and the two-column form (``widgets/form_grid.py``).

Measured on built widgets under the application stylesheet: where the label is drawn relative to the control, the
ink and size the label role gives it, and whether every row of a form starts its control at the same x. And the
phone form (``BandForm``) still finds a label set above its control and moves it into its label column, as it does
for the caption ``ControlBand.add_scope`` bundles beside one.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton

from ui import theme
from widgets.form_grid import FormGrid
from widgets.labelled_field import LabelledField


@pytest.fixture
def sheet(qapp):
    theme.apply_theme(theme.DEFAULT_THEME)
    qapp.setStyleSheet(theme.qss())
    yield theme
    theme.apply_theme("blue")
    qapp.setStyleSheet("")


def _settle():
    for _ in range(3):
        QApplication.processEvents()


def _origin(w, ref):
    return w.mapTo(ref, w.rect().topLeft())


def test_the_label_sits_above_its_control_in_the_label_role(qtbot, sheet):
    combo = QComboBox()
    combo.addItems(["GFP", "RFP"])
    field = LabelledField("Channel", combo)
    qtbot.addWidget(field)
    field.show()
    _settle()
    label = field.label()
    assert label.text() == "Channel" and ":" not in label.text()
    assert combo.currentText() == "GFP", "the control holds only its value"
    assert _origin(label, field).y() + label.height() <= _origin(combo, field).y()
    assert _origin(label, field).x() == _origin(combo, field).x()
    assert label.font().pixelSize() == sheet.Typography.small_size
    assert label.palette().color(QPalette.WindowText).name().upper() == sheet.Colors.text_secondary.upper()
    assert label.buddy() is combo


def test_beside_puts_the_label_left_of_the_control(qtbot, sheet):
    edit = QLineEdit()
    field = LabelledField("Folder", edit, above=False)
    qtbot.addWidget(field)
    field.show()
    _settle()
    label = field.label()
    assert _origin(label, field).x() + label.width() <= _origin(edit, field).x()


def test_every_row_of_a_form_starts_its_control_at_one_x(qtbot, sheet):
    form = FormGrid()
    qtbot.addWidget(form)
    short, long_ = QLineEdit(), QComboBox()
    browse = QPushButton("Browse…")
    form.add_eyebrow("Input")
    form.add_row("Folder", short, browse, help="The folder holding one archive per well.")
    form.add_row("Segmentation method", long_)
    check = QCheckBox("Also add channels found at run time")
    form.add_wide(check)
    form.resize(420, 200)
    form.show()
    _settle()
    xs = {_origin(short, form).x(), _origin(long_, form).x()}
    assert len(xs) == 1, f"controls start at {xs}"
    assert xs.pop() >= sheet.Metrics.form_label_w
    assert _origin(browse, form).x() > _origin(short, form).x() + short.width() - 1
    assert _origin(check, form).x() == 0, "a self-labelling control spans both columns"
    labels = {lb.text(): lb for lb in form.findChildren(QLabel)}
    assert labels["INPUT"].property("textRole") == "eyebrow"
    assert labels["The folder holding one archive per well."].property("textRole") == "help"
    assert form.row_count() == 5


def test_the_phone_form_moves_a_label_set_above_its_control_into_its_column(qtbot, sheet):
    from widgets.control_band import ControlBand

    band = ControlBand()
    qtbot.addWidget(band)
    combo = QComboBox()
    combo.addItems(["Mean", "Median"])
    band.add_scope("", LabelledField("Metric", combo))
    band.resize(380, 200)
    band.show()
    _settle()
    band.setForm(True, label_width=96, row_height=36, gap=8)
    _settle()
    rows = band.form().rows("Data")
    assert len(rows) == 1
    row, item = rows[0]
    column = [lb for lb in row.findChildren(QLabel) if lb.parentWidget() is row]
    assert [lb.text() for lb in column] == ["Metric"] and column[0].width() == 96
    band.setForm(False)
    _settle()
    field = band.findChild(LabelledField)
    assert field.label().parentWidget() is field, "the label goes back above its control"
    assert field.layout().indexOf(field.label()) == 0


def test_a_form_grid_puts_each_label_above_its_controls_on_a_phone(qapp):
    """Its declared phone form (``widgets.form_registry``): every row laid out again with the label above, the
    one arrangement a phone's width holds — and back beside it at the desk, each row's hidden state kept."""
    from PySide6.QtWidgets import QLineEdit

    from widgets import form_registry
    from widgets.form_grid import FormGrid

    form = FormGrid()
    field, other = QLineEdit(), QLineEdit()
    cap = form.add_row("Experiment name", field)
    form.add_row("Workers", other)
    other.setHidden(True)
    form.resize(420, 200)
    form.show()
    _settle()
    assert cap.geometry().right() < field.geometry().left()
    arrange = form_registry.custom_apply(form)
    arrange(True)
    _settle()
    assert cap.geometry().bottom() < field.geometry().top() and cap.geometry().left() == field.geometry().left()
    arrange(False)
    _settle()
    assert cap.geometry().right() < field.geometry().left()
    assert other.isHidden(), "laying the grid out again showed a row the page had hidden"
    form.close()
