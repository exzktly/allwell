"""PlotCard hover 'save to Saved Plots' button wiring."""
from __future__ import annotations

import pytest

from widgets.plot_card import PlotCard

pytestmark = pytest.mark.qt


def test_save_button_hidden_until_handler_set(qapp):
    card = PlotCard(figsize=(3, 2))
    card.figure.add_subplot(1, 1, 1)
    # No handler → save button is not among the hover buttons.
    assert card._save_btn not in card._hover_buttons()
    card.set_save_handler(lambda png, label: "ok")
    # Single-axis figure still offers the save button (unlike copy).
    assert card._save_btn in card._hover_buttons()


def test_save_click_invokes_handler_with_png_bytes(qapp):
    card = PlotCard(figsize=(3, 2))
    ax = card.figure.add_subplot(1, 1, 1)
    ax.plot([0, 1, 2], [0, 1, 4])
    captured = {}

    def handler(png_bytes, label):
        captured["png"] = png_bytes
        captured["label"] = label
        return "Saved"

    card.set_save_handler(handler)
    card._hover_ax = ax            # emulate hovering the panel
    card._on_save_axis_clicked()

    assert captured.get("png", b"").startswith(b"\x89PNG")
    assert "label" in captured
