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


def test_save_figure_button_multi_axis_only(qapp):
    """The whole-figure save button appears only on multi-axis figures (a
    single-axis figure's per-panel save already captures the whole figure)."""
    card = PlotCard(figsize=(3, 4))
    ax0 = card.figure.add_subplot(2, 1, 1)
    ax0.plot([0, 1], [0, 1])
    card.figure.add_subplot(2, 1, 2).plot([0, 1], [1, 0])
    # No handler → neither save button is offered.
    assert card._save_fig_btn not in card._hover_buttons()
    card.set_save_handler(lambda png, label: "ok")
    # Multi-axis + handler → both the per-panel and whole-figure buttons show.
    btns = card._hover_buttons()
    assert card._save_btn in btns
    assert card._save_fig_btn in btns

    # Single-axis figure → whole-figure button is suppressed (would duplicate the
    # per-panel save).
    single = PlotCard(figsize=(3, 2))
    single.figure.add_subplot(1, 1, 1).plot([0, 1], [0, 1])
    single.set_save_handler(lambda png, label: "ok")
    assert single._save_btn in single._hover_buttons()
    assert single._save_fig_btn not in single._hover_buttons()


def test_save_figure_click_captures_whole_figure(qapp):
    """Clicking the whole-figure save button invokes the handler with PNG bytes
    for the entire figure (uses the figure suptitle as the label)."""
    card = PlotCard(figsize=(3, 4))
    card.figure.add_subplot(2, 1, 1).plot([0, 1], [0, 1])
    card.figure.add_subplot(2, 1, 2).plot([0, 1], [1, 0])
    card.figure.suptitle("Whole set")
    captured = {}

    def handler(png_bytes, label):
        captured["png"] = png_bytes
        captured["label"] = label
        return "Saved"

    card.set_save_handler(handler)
    card._on_save_figure_clicked()

    assert captured.get("png", b"").startswith(b"\x89PNG")
    assert captured.get("label") == "Whole set"
