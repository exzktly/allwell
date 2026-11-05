"""Where the Nanowells page decides what phone layout changes.

The page lays out differently on a phone: the site detail takes the whole page
while a nanowell is selected and the crop strip wraps to its own shape. Those
decisions are made here and nowhere else in the feature, so the mode is read in
one place (``tests/test_phone_mode_stays_in_views.py``) and the controller and
the tab view only call. The renderer never reads the mode: what a narrow or short
figure needs (a footer broken to its width, a key clear of the titles) it derives
from the figure's own size in points, so a phone's figure and a small window's get
the same treatment.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer

from core import phone_layout

def on_phone() -> bool:
    """Whether phone layout is on."""
    return phone_layout.is_on()


def strip_columns_for(fig, crops) -> int:
    """The crop strip's columns: one row at a desk (``0``), and in phone layout as many rows as make each crop
    largest in the strip's own figure (``crop_grid_compute.strip_columns``)."""
    from .crop_grid_compute import strip_columns

    if not crops or not on_phone():
        return 0
    width, height = fig.get_size_inches()
    return strip_columns(len(crops), width * 0.94, height * 0.78)


def detail_placement(card, detail, detail_layout, strip_card, *, redraw_figure: Callable[[], None],
                     refit_strip: Callable[[], None], refit_ms: int) -> Callable[[bool], None]:
    """``show_detail(on)``: show the site detail beside the figure — or, in phone layout, instead of it.

    A phone is one column wide: beside the detail the figure was squeezed to a strip a few pixels wide, with no dot left
    to tap. So there the detail takes the page while a nanowell is selected, and ✕ (which clears the selection) brings
    the figure back. Switching phone layout with a nanowell selected re-places them at once, and the crop strip, which
    wraps its crops to its own shape in phone layout (:func:`strip_columns_for`), is laid out again once a resize of it
    settles — the pane going full-screen, the phone turned.

    *redraw_figure* draws the distribution figure, which was not drawn while hidden; *refit_strip* lays the strip out
    again, once its resizes have been quiet for *refit_ms* (the pane going full-screen, the phone turned, arrive as a
    burst)."""
    from core.safe import swallow

    def show_detail(on: bool) -> None:
        detail.setVisible(on)
        phone = on_phone()
        was_hidden = card.isHidden()
        card.setVisible(not (on and phone))
        if was_hidden and not card.isHidden():
            # Hidden, the figure was not drawn; it is drawn now that it is back. With the card as the timer's context,
            # Qt drops the call if the page is deleted before it runs.
            QTimer.singleShot(0, card, redraw_figure)
        # Alone on a phone the detail has the height to give its crops; beside the figure the series keep most of it.
        detail_layout.setStretchFactor(strip_card, 3 if phone else 1)

    def follow_phone(_on: bool) -> None:
        with swallow("Nanowells: re-place the site detail for phone layout"):
            show_detail(not detail.isHidden())

    phone_layout.follow(card, follow_phone)
    strip_refit = QTimer(strip_card)
    strip_refit.setSingleShot(True)
    strip_refit.setInterval(refit_ms)
    strip_refit.timeout.connect(refit_strip)
    strip_card.canvas.mpl_connect("resize_event", lambda _event: strip_refit.start())
    return show_detail
