"""The Nanowells page's Subset editor at a phone's size, through the page's real builder in a minimal shell.

Reported: "Subset definitions are completely broken in layout on mobile in nanowell distribution". Rendered on a
432 x 936 phone, every line of the editor was a fixed row: the heading was cut to "Use the nanowe … of these
conditi", a condition's combos to "Star" and "betw", the name box to "Name thi…" (one condition asked for 680-814 px
of the phone's 432).

The editor is a full-height page of the inspector now (the band's **Conditions** row opens it), and in phone
layout the inspector is the Controls sheet: the sheet is the editor's, so it never shares a page with the figure and
never opens under the sheet it was pressed in. These tests give the inspector a phone-sized host — a window of the
sheet's size holding it, registered as the phone's host — and measure the editor there.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from review.core import inspector_service as svc
from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset

UPRIGHT = (432, 818)                                     # the Controls sheet of a 432 x 936 phone, held upright
SIDEWAYS = (936, 381)                                    # and held sideways


@pytest.fixture
def page(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    _loaded(qtbot, ctx)
    return ctx


class _Sheet:
    """The phone's host for the inspector, as ``PhoneShell`` registers one: a surface of the sheet's size."""

    def __init__(self, qtbot, ctx, size):
        self._ctx = ctx
        self.holder = QWidget()
        qtbot.addWidget(self.holder)
        lay = QVBoxLayout(self.holder)
        lay.setContentsMargins(0, 0, 0, 0)
        self.holder.resize(*size)

    def open_inspector(self) -> bool:
        insp = svc.inspector(self._ctx)
        self.holder.layout().addWidget(insp)
        insp.show()
        self.holder.show()
        return True

    def close_inspector(self) -> None:
        self.holder.hide()

    def inspector_is_open(self) -> bool:
        return self.holder.isVisible()


def _open(qtbot, ctx, monkeypatch, size, extra=0):
    """The editor open in a sheet of *size* with three conditions (and *extra* more counts) — a metric read at a
    timepoint, a count, and a range — laid out. Returns ``(editor, sheet)``."""
    from core import phone_layout

    sheet = _Sheet(qtbot, ctx, size)
    svc.set_phone_host(ctx, sheet)
    monkeypatch.setattr(phone_layout, "is_on", lambda: True)
    ctx.widgets.get("_nw_subset_edit_btn").click()
    editor = ctx.widgets.get("_nw_subset_editor")
    for _ in range(3):
        editor.add_btn.click()
    first, _second, third = editor.rows
    first.field_cb.setCurrentIndex(first.field_cb.findData("count_fc"))
    third.op_cb.setCurrentIndex(third.op_cb.findData("between"))
    for _ in range(extra):
        editor.add_btn.click()
    qtbot.wait(50)
    return editor, sheet


def _controls(editor):
    # By type, never ``findChildren(QWidget)``: that wraps each combo's popup container, a top-level window, which the
    # suite's widget reaper then deletes a second time (a segfault at teardown).
    found = [w for kind in (QComboBox, QAbstractSpinBox, QLineEdit, QPushButton, QToolButton, QLabel)
             for w in editor.findChildren(kind)]
    return [w for w in found if w.isVisibleTo(editor) and not isinstance(w.parentWidget(), (QComboBox, QAbstractSpinBox))]


def test_on_an_upright_phone_no_control_of_the_editor_is_cut(qtbot, page, monkeypatch):
    editor, sheet = _open(qtbot, page, monkeypatch, UPRIGHT)
    assert sheet.holder.isAncestorOf(editor) and editor.isVisibleTo(sheet.holder)
    # Fixed rows asked for 680-814 px and held the window that wide: nothing was cut only because nothing was a phone.
    need = editor.minimumSizeHint().width()
    assert need <= UPRIGHT[0], f"the editor asks for {need} px, wider than the phone"
    assert editor.width() <= UPRIGHT[0]
    row = editor.rows[0]
    assert row.at_cb.isVisibleTo(editor) and editor.rows[2].b_spin.isVisibleTo(editor)
    body = editor
    cut = []
    for w in _controls(editor):
        right = w.mapTo(body, w.rect().topRight()).x()
        need = w.minimumSizeHint() if isinstance(w, QLabel) and w.wordWrap() else w.sizeHint()
        if w.width() < need.width() or w.height() < w.minimumSizeHint().height() or right > body.width():
            cut.append(f"{type(w).__name__} {getattr(w, 'text', lambda: '')() or w.objectName()!r}: "
                       f"{w.width()}x{w.height()} of {need.width()}x{w.minimumSizeHint().height()}, right edge {right} "
                       f"of {body.width()}")
    assert not cut, "cut at phone width:\n" + "\n".join(cut)


def test_the_editor_is_a_full_height_page_that_scrolls_inside_the_sheet(qtbot, page, monkeypatch):
    """Held sideways the sheet is 381 px tall and eight conditions need more: the page asks for the sheet's full
    height, every condition keeps its own height (none squeezed — they once were, to half), and the page scrolls
    down, never sideways."""
    editor, sheet = _open(qtbot, page, monkeypatch, SIDEWAYS, extra=5)
    insp = svc.inspector(page)
    assert insp.wantedSize() == "full"
    slot = insp.currentSlot()
    assert slot.widget().isAncestorOf(editor)
    qtbot.waitUntil(lambda: slot.verticalScrollBar().maximum() > 0, timeout=2000)
    assert slot.horizontalScrollBar().maximum() == 0
    assert editor.height() > slot.viewport().height(), "eight conditions fit a sideways phone without scrolling?"
    squeezed = [r for r in editor.rows if r.height() < r.minimumSizeHint().height()]
    assert not squeezed, f"{len(squeezed)} of {len(editor.rows)} conditions are squeezed below their height"
    # A condition added below the fold is scrolled to: the page was at its top, and moves.
    bar = slot.verticalScrollBar()
    bar.setValue(0)
    qtbot.wait(20)
    assert bar.value() == 0
    editor.add_btn.click()
    last = editor.rows[-1]
    qtbot.waitUntil(lambda: bar.value() > 0, timeout=2000)
    top = last.mapTo(slot.viewport(), last.rect().topLeft()).y()
    assert 0 <= top and top + last.height() <= slot.viewport().height(), "the new condition is not on screen"


def test_on_a_sideways_phone_the_editor_has_the_sheet_and_the_figure_keeps_the_page(qtbot, page, monkeypatch):
    """Held sideways the page is 381 px tall. The editor used to take the page from the figure there; in the sheet
    it takes nothing from the page, so the figure stays where it is and comes back with nothing to redraw."""
    editor, sheet = _open(qtbot, page, monkeypatch, SIDEWAYS)
    figure = page.widgets.get("_nw_card")
    assert figure.isVisibleTo(page.window), "the editor's page took the figure off the page"
    assert sheet.holder.isAncestorOf(editor)
    svc.close(page)
    assert not sheet.inspector_is_open() and figure.isVisibleTo(page.window)


def test_at_a_desk_the_editor_is_beside_the_figure(qtbot, page):
    page.widgets.get("_nw_subset_edit_btn").click()
    editor = page.widgets.get("_nw_subset_editor")
    for _ in range(3):
        editor.add_btn.click()
    first = editor.rows[0]
    first.field_cb.setCurrentIndex(first.field_cb.findData("count_fc"))
    figure = page.widgets.get("_nw_card")
    assert figure.isVisibleTo(page.window)
    insp = svc.inspector(page)
    assert svc.is_open(page) and insp.currentTab() == "data" and insp.isAncestorOf(editor)
    # Beside, not over: the inspector's left edge is at or past the figure's right.
    left = insp.mapTo(page.window, insp.rect().topLeft()).x()
    right = figure.mapTo(page.window, figure.rect().topRight()).x()
    assert left >= right, f"the inspector ({left}) overlaps the figure ({right})"
    assert len(editor.rows) == 3 and editor.written().conditions[0].field == "count_fc"


def test_the_editor_has_no_close_of_its_own_and_the_row_leaves_it(qtbot, page):
    """One Close and one Back: the inspector's. Pressing the row again leaves the page."""
    button = page.widgets.get("_nw_subset_edit_btn")
    editor = page.widgets.get("_nw_subset_editor")
    assert not hasattr(editor, "close_btn")
    assert not [b for b in editor.findChildren(QPushButton) if b.text() == "Close"]
    button.click()
    assert svc.inspector(page).isAncestorOf(editor)
    button.click()
    assert not svc.inspector(page).isAncestorOf(editor)


def test_choosing_custom_subset_opens_the_editor_and_changes_nothing(qtbot, page):
    """*Custom subset…* is on the one list with the presets and the saved subsets; choosing it shows the conditions
    of the subset in use and applies nothing."""
    from review.features.nanowell import starting_cells_compute as sc

    combo = page.widgets.get("_nw_subset_cb")
    editor = page.widgets.get("_nw_subset_editor")
    st = page.state.feature_slice("nanowell")
    before = st.subset
    labels = [combo.itemText(i) for i in range(combo.count()) if combo.itemText(i)]
    assert labels[0] == "All nanowells" and labels[-1] == "Custom subset…"
    assert not svc.inspector(page).isAncestorOf(editor)
    combo.setCurrentIndex(combo.findData(sc.CUSTOM))
    insp = svc.inspector(page)
    assert svc.is_open(page) and insp.isAncestorOf(editor) and insp.currentTab() == "data"
    assert st.subset == before


def test_the_row_reads_how_many_conditions_the_subset_has(qtbot, page):
    from review.features.nanowell import controller as nwc
    from review.features.nanowell import subset_compute as ss

    button = page.widgets.get("_nw_subset_edit_btn")
    assert button.text() == "Conditions ›"
    nwc.set_subset(page, ss.Subset((ss.Condition("n0", "ge", 1), ss.Condition("n0", "le", 2))))
    assert button.text() == "Conditions: 2 ›"


def test_a_redraw_with_the_editor_open_leaves_it_open(qtbot, page, monkeypatch):
    from review.features.nanowell import controller as nwc

    editor, sheet = _open(qtbot, page, monkeypatch, SIDEWAYS)
    nwc.redraw(page)
    qtbot.wait(20)
    assert editor.isVisibleTo(sheet.holder)
    assert page.widgets.get("_nw_card").isVisibleTo(page.window)
