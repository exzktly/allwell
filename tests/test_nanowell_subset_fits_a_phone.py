"""The Nanowells page's Subset editor at a phone's size, through the page's real builder in a minimal shell.

Reported: "Subset definitions are completely broken in layout on mobile in nanowell distribution". Rendered on a
432 x 936 phone, every line of the editor was a fixed row: the heading was cut to "Use the nanowe … of these
conditi", a condition's combos to "Star" and "betw", the name box to "Name thi…" (one condition asked for 680-814 px
of the phone's 432). With three conditions the editor needed 858 px of the 818 px page and every row was squeezed to
half its height. Held sideways, the page is 381 px tall and the figure and its footer alone need 351 of them, so the
editor was drawn over the figure.

The phone shell lends the page's band to its Controls sheet, which hides it while the page is on screen; these tests
hide the band the same way, so the page's height is the editor's and the figure's to share, as on a phone.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLabel, QLineEdit, QPushButton, QToolButton

from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset

UPRIGHT = (432, 818)                                     # the Review page of a 432 x 936 phone, held upright
SIDEWAYS = (936, 381)                                    # and held sideways


@pytest.fixture
def page(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    _loaded(qtbot, ctx)
    return ctx


def _band(ctx):
    from widgets.control_band import ControlBand

    (band,) = [b for b in ctx.window.findChildren(ControlBand) if b.objectName() == "ControlBand"
               and b.isAncestorOf(ctx.widgets.get("_nw_subset_cb"))]
    return band


def _open(qtbot, ctx, size, extra=0):
    """The editor open with three conditions (and *extra* more counts) — a metric read at a timepoint, a count, and a range — on a page of
    *size*, laid out."""
    _band(ctx).hide()
    ctx.window.resize(*size)
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
    return editor


def _controls(editor):
    # By type, never ``findChildren(QWidget)``: that wraps each combo's popup container, a top-level window, which the
    # suite's widget reaper then deletes a second time (a segfault at teardown).
    found = [w for kind in (QComboBox, QAbstractSpinBox, QLineEdit, QPushButton, QToolButton, QLabel)
             for w in editor.findChildren(kind)]
    return [w for w in found if w.isVisibleTo(editor) and not isinstance(w.parentWidget(), (QComboBox, QAbstractSpinBox))]


def test_on_an_upright_phone_no_control_of_the_editor_is_cut(qtbot, page):
    editor = _open(qtbot, page, UPRIGHT)
    # Fixed rows asked for 680-814 px and held the window that wide: nothing was cut only because nothing was a phone.
    need = editor.minimumSizeHint().width()
    assert need <= UPRIGHT[0], f"the editor asks for {need} px, wider than the phone"
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


def test_beside_the_figure_the_editor_keeps_its_share_and_scrolls(qtbot, page):
    """Upright there is room for both: the figure stays, and the editor takes no more than its share, scrolling."""
    editor = _open(qtbot, page, UPRIGHT, extra=5)
    figure = page.widgets.get("_nw_card")
    assert figure.isVisibleTo(page.window)
    plot_area = editor.parentWidget()
    assert editor.height() <= plot_area.height() * 0.45 + 1
    assert editor.scroll.verticalScrollBar().maximum() > 0, "eight conditions fit a phone's share without scrolling?"
    assert editor.geometry().bottom() < figure.mapTo(plot_area, figure.rect().topLeft()).y()


def test_on_a_sideways_phone_the_editor_takes_the_page_and_close_brings_the_figure_back(qtbot, page):
    editor = _open(qtbot, page, SIDEWAYS)
    figure = page.widgets.get("_nw_card")
    assert not figure.isVisibleTo(page.window), "the figure kept its place on a page too short for both"
    assert editor.height() >= editor.close_btn.height() * 4, f"the editor alone on the page is {editor.height()} px"
    editor.close_btn.click()
    qtbot.wait(20)
    assert editor.isHidden() and figure.isVisibleTo(page.window)
    fig = page.widgets.get("_nw_fig")
    qtbot.waitUntil(lambda: any(c.get_gid() == "nw-dots" for ax in fig.axes for c in ax.collections), timeout=5000)


def test_at_a_desk_the_figure_stays_beside_the_editor(qtbot, page):
    editor = _open(qtbot, page, (1500, 900))
    assert page.widgets.get("_nw_card").isVisibleTo(page.window)
    assert editor.scroll.verticalScrollBar().maximum() == 0, "a desk's three conditions should not scroll"
    assert len(editor.rows) == 3 and editor.written().conditions[0].field == "count_fc"
