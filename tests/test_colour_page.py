"""The colour page: the colour that reaches the caller, from each of its three ways in."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from widgets.colour_page import ColourPage, hex_of


@pytest.fixture
def page(qtbot):
    w = ColourPage(colors=["#F59E0B", "#4ADE80", "#6B8AFD"])
    qtbot.addWidget(w)
    w.setColor("#4ADE80")
    return w


def test_filling_the_page_emits_nothing_and_shows_the_colour_everywhere(page):
    seen: list[str] = []
    page.colorPicked.connect(lambda c: seen.append(hex_of(c)))
    page.setColor("#6B8AFD")
    assert seen == []
    assert hex_of(page.color()) == "#6B8AFD" and page.hex_edit.text() == "#6B8AFD"


def test_a_swatch_reaches_the_caller_as_that_colour(page):
    seen: list[str] = []
    page.colorPicked.connect(lambda c: seen.append(hex_of(c)))
    page.swatches.colorPicked.emit(QColor("#F59E0B"))
    assert seen == ["#F59E0B"] and page.hex_edit.text() == "#F59E0B"


def test_a_hex_value_reaches_the_caller_and_a_bad_or_unchanged_one_does_not(page):
    seen: list[str] = []
    page.colorPicked.connect(lambda c: seen.append(hex_of(c)))
    page.hex_edit.setText("#102030")
    page.hex_edit.editingFinished.emit()
    assert seen == ["#102030"]
    page.hex_edit.editingFinished.emit()            # the same value again
    page.hex_edit.setText("#12")                    # not a colour
    page.hex_edit.editingFinished.emit()
    assert seen == ["#102030"]


def test_the_custom_picker_is_on_the_page_and_its_hue_reaches_the_caller(page):
    """The custom picker is part of the page — no second surface — and moving
    the hue with the saturation and value held changes the colour's hue."""
    from widgets.popover import Popover
    assert page.isAncestorOf(page.sv_square) and page.isAncestorOf(page.hue_strip)
    assert not page.findChildren(Popover)
    seen: list[QColor] = []
    page.colorPicked.connect(seen.append)
    page.setColor("#FF0000")
    page.hue_strip.setHue(1 / 3)
    page.hue_strip.hueChanged.emit(1 / 3)
    assert len(seen) == 1
    hue = seen[0].getHsvF()[0]
    assert hue == pytest.approx(1 / 3, abs=0.01), "the hue strip did not move the colour to green"
    assert page.hex_edit.text() == hex_of(seen[0])


# ── shown as an inspector page (review/core/inspector_pages.open_colour) ──────

@pytest.fixture
def swatch_ctx(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton, QSplitter, QVBoxLayout, QWidget

    from tests._minimal_shell import minimal_shell
    ctx, window = minimal_shell(qtbot)
    split = QSplitter(Qt.Horizontal)
    split.addWidget(QWidget())
    host = QWidget()
    QVBoxLayout(host)
    swatch = QPushButton("", host)
    host.layout().addWidget(swatch)
    split.addWidget(host)
    window.setCentralWidget(split)
    window._h_pane = split
    window.resize(1200, 700)
    window.show()
    return ctx, window, swatch


def test_a_swatch_button_opens_one_page_seeded_with_its_colour_and_a_second_press_leaves(swatch_ctx):
    from review.core import inspector_pages, inspector_service
    from tests import _surface_checks as surf
    ctx, window, swatch = swatch_ctx
    picked: list[str] = []
    page = inspector_pages.open_colour(ctx, swatch, key="k", title="Boundary colour", color="#F59E0B",
                                       on_pick=lambda c: picked.append(hex_of(c)))
    insp = inspector_service.inspector(ctx)
    assert insp.currentWidget() is page and insp.title_label.text() == "Boundary colour"
    assert hex_of(page.color()) == "#F59E0B"
    assert surf.depth(window) == 0, "a colour opened a surface over the page"
    page.hex_edit.setText("#00FF00")
    page.hex_edit.editingFinished.emit()
    assert picked == ["#00FF00"]
    assert inspector_pages.open_colour(ctx, swatch, key="k", title="Boundary colour", color="#00FF00",
                                       on_pick=lambda c: None) is None
    assert not insp.holds(page)
    # Opened again it is the same page, re-seeded, with the new handler.
    again: list[str] = []
    assert inspector_pages.open_colour(ctx, swatch, key="k", title="Boundary colour", color="#112233",
                                       on_pick=lambda c: again.append(hex_of(c))) is page
    assert hex_of(page.color()) == "#112233"
    page.swatches.colorPicked.emit(QColor("#4ADE80"))
    assert again == ["#4ADE80"] and picked == ["#00FF00"]


def test_the_segmentation_colour_swatch_sets_the_element_s_colour_through_the_page(swatch_ctx):
    """The real picker function, from the real swatch registry: the colour
    picked on the page is the colour ``review_image_get_color`` then returns."""
    from review.core import inspector_service
    from review.features.image_browser.review_image import controller as ric
    ctx, _window, swatch = swatch_ctx
    ctx.runtime.review_image.color_swatches = {"boundary": swatch}
    before = tuple(int(v) for v in ric.review_image_get_color(ctx, "boundary"))
    ric.pick_review_image_color(ctx, "boundary")
    page = inspector_service.inspector(ctx).currentWidget()
    assert tuple(page.color().getRgb()[:3]) == before
    target = (1, 200, 3) if before != (1, 200, 3) else (3, 200, 1)
    page.hex_edit.setText("#%02X%02X%02X" % target)
    page.hex_edit.editingFinished.emit()
    assert tuple(int(v) for v in ric.review_image_get_color(ctx, "boundary")) == target
