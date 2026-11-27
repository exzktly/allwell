"""``widgets.card.Card`` draws the design language's card: the card fill and a hairline edge (§2.1 rule 5), in
every palette, with its plain containers transparent so a row inside does not paint the canvas across it."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import theme
from widgets.card import Card


@pytest.fixture(params=theme.available_themes())
def sheet(qapp, request):
    theme.apply_theme(request.param)
    qapp.setStyleSheet(theme.qss())
    yield theme.Colors
    theme.apply_theme("blue")
    qapp.setStyleSheet("")


def _grab(qapp, card):
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(20, 20, 20, 20)
    lay.addWidget(card)
    host.resize(320, 200)
    host.show()
    qapp.processEvents()
    img, g = host.grab().toImage(), card.geometry()
    host.close()
    return img, g, host          # the host owns the card: kept alive while the caller reads it


def test_a_card_draws_its_fill_and_hairline(qapp, sheet):
    card = Card("Run")
    img, g, _host = _grab(qapp, card)
    inside = img.pixelColor(g.left() + 6, g.center().y()).name().upper()
    edge = img.pixelColor(g.left(), g.center().y()).name().upper()
    assert inside == sheet.card.upper()
    assert edge == sheet.hairline.upper()


def test_a_plain_row_inside_does_not_paint_the_canvas(qapp, sheet):
    card = Card("Folders")
    row = QWidget()
    QHBoxLayout(row).addWidget(QLabel("Source dataset"))
    card.body().addWidget(row)
    img, g, _host = _grab(qapp, card)
    at = row.mapTo(card, row.rect().center())
    probe = img.pixelColor(g.left() + row.mapTo(card, row.rect().topRight()).x() - 4, g.top() + at.y()).name()
    assert probe.upper() == sheet.card.upper(), "a bare row painted a band across the card"


def test_the_heading_carries_title_and_commands():
    from ui import buttons
    show = buttons.quiet("Show log")
    card = Card("Run", commands=(show,))
    assert card.title() == "Run" and show.parentWidget() is card
    assert card.heading().indexOf(show) > 0
