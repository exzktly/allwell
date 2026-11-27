"""The Nanowells page's **Display range**: the brightness window its pictures of the image channel are drawn with.

Asked for: a display range for the nanowell channels "just like the other places in the app" — the *min* / *max*
window with an **Auto** that Review Image, the Image Table and the Cell Browser give a channel — and, once set
anywhere on the page, kept that way for every render until it is changed again.

So it is the page's own setting (``NanowellState.display_ranges``, one range per image channel, kept with the page's
settings), not the per-channel store the other viewers share: Review Image writes its automatic window into that
store whenever it draws, which would change the page's range without anyone touching it.

One setting, offered where the page offers its image channel — beside the crop strip, on the Extremes view and
beside the Array view's Image — as a button naming the range in force that opens the *min* / *max* / **Auto** row in
a popover. Blank fields are Auto: each picture takes its own percentile scale, as it did before there was a control.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from review.features.nanowell import controller as nwc

#: The buttons that open the Display range, one per place the image channel is offered (``tab_view``'s
#: ``_IMAGE_CHANNEL_COMBOS``): refreshed together by :func:`refresh_display_range_buttons`.
DISPLAY_RANGE_BUTTONS = ("_nw_strip_range_btn", "_nw_extremes_range_btn", "_nw_array_range_btn", "_nw_popout_range_btn")

RANGE_TIP = ("The brightness window the crops, the pop-out, the Extremes montage and the Array view's full-resolution "
        "image of this channel are drawn with, in the image's own units, kept until you change it. Blank is Auto: "
        "each picture is stretched from the 1st to the 99th percentile of its pixels.")


def _number(text: str) -> float | None:
    try:
        return float(str(text).strip())
    except ValueError:
        return None


def _fmt(value) -> str:
    return "" if value is None else f"{value:g}"


def button_text(ctx) -> str:
    """What a Display range button says: the range in force for the page's image channel, or Auto."""
    chosen = nwc.display_range(ctx)
    return "Range: auto" if chosen is None else f"Range: {_fmt(chosen[0])}–{_fmt(chosen[1])}"


def build_display_range_button(ctx, name: str) -> QPushButton:
    """One of the :data:`DISPLAY_RANGE_BUTTONS`; a click opens the *min* / *max* / **Auto** row under it."""
    button = QPushButton(button_text(ctx))
    button.setProperty("variant", "secondary")
    button.setToolTip(RANGE_TIP)
    button.setObjectName(name)
    button.clicked.connect(lambda _=False: open_display_range(ctx, button))
    ctx.widgets.register(name, button)
    return button


#: Registry key of the page's one range page (``widgets.range_page.RangePage``).
RANGE_PAGE = "_nw_range_page"


def open_display_range(ctx, anchor) -> None:
    """Show the Display range page in the inspector, seeded from the range in force. *min* and *max* apply when
    either is edited and both are numbers, the high above the low; **Auto** clears them and gives the pictures back
    their own scale; **Reset** returns to the range the page opened with. Pressing the button again leaves the page."""
    from review.core import inspector_service
    from widgets.range_page import RangePage

    page = ctx.widgets.get_or_none(RANGE_PAGE)
    if page is None:
        page = RangePage(ctx.window, auto_tip="Back to each picture's own scale (the 1st to 99th percentile "
                                              "of its pixels).")
        page.hide()
        page.changed.connect(lambda low, high: nwc.set_display_range(ctx, low, high))
        ctx.widgets.register(RANGE_PAGE, page)
        ctx.widgets.register("_nw_range_lo_edit", page.low_edit)
        ctx.widgets.register("_nw_range_hi_edit", page.high_edit)
        ctx.widgets.register("_nw_range_auto_btn", page.auto_button)
    # The same button again leaves the page; another of the page's Display
    # range buttons keeps it open and re-reads the range.
    same_button = page.property("openedFrom") == anchor.objectName() and bool(anchor.objectName())
    if inspector_service.is_showing(ctx, page) and same_button:
        inspector_service.pop(ctx)
        return
    page.setProperty("openedFrom", anchor.objectName())
    chosen = nwc.display_range(ctx)
    page.setCaption(f"{nwc.display_key(ctx.state.feature_slice('nanowell')).upper()} — the brightness window "
                    "every picture on this page is drawn in.")
    page.setRange(None if chosen is None else chosen[0], None if chosen is None else chosen[1])
    inspector_service.push(ctx, "nanowell/display_range", "Display range", page, tab="display", anchor=anchor)


def refresh_display_range_buttons(ctx) -> None:
    """Every Display range button naming the range in force for the page's image channel now."""
    text = button_text(ctx)
    for name in DISPLAY_RANGE_BUTTONS:
        button = ctx.widgets.get_or_none(name)
        if button is not None:
            button.setText(text)
