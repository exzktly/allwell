"""The Nanowells page's **Display range**: the brightness window its pictures of the image channel are drawn with.

Asked for: a display range for the nanowell channels "just like the other places in the app". Review Image, the
Image Table and the Cell Browser each give a channel a *min* / *max* window with an **Auto** that hands it back to
the image, and they share one per-channel store (``AppState.image_view``), so a window tuned in one is the default
in the others. The page joins that store (``controller.display_key``): a range set here is the one those viewers
show for the channel, and theirs is the one this page draws with.

One setting, offered where the page offers its image channel — beside the crop strip, on the Extremes view and
beside the Array view's Image — as a button naming the range in force that opens the *min* / *max* / **Auto** row in
a popover. Blank fields are Auto: each picture takes its own percentile scale, as it did before there was a control.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from review.features.nanowell import controller as nwc
from ui import theme

#: The buttons that open the Display range, one per place the image channel is offered (``tab_view``'s
#: ``_IMAGE_CHANNEL_COMBOS``): refreshed together by :func:`refresh_display_range_buttons`.
DISPLAY_RANGE_BUTTONS = ("_nw_strip_range_btn", "_nw_extremes_range_btn", "_nw_array_range_btn")

_TIP = ("The brightness window the crops, the pop-out, the Extremes montage and the Array view's full-resolution "
        "image of this channel are drawn with, in the image's own units. Blank is Auto: each picture is stretched "
        "from the 1st to the 99th percentile of its pixels.\n\nThe range is the channel's own across the app: Review "
        "Image, the Image Table and the Cell Browser show the same one.")


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
    button.setToolTip(_TIP)
    button.clicked.connect(lambda _=False: open_display_range(ctx, button))
    ctx.widgets.register(name, button)
    return button


def open_display_range(ctx, anchor) -> None:
    """Open the Display range row under *anchor*, seeded from the range in force. *min* and *max* apply when either
    is edited and both are numbers, the high above the low; **Auto** clears them and gives the pictures back their
    own scale."""
    from widgets.popover import Popover

    chosen = nwc.display_range(ctx)
    pop = Popover(anchor)
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(QLabel(f"{nwc.display_key(ctx.state.feature_slice('nanowell')).upper()}  min"))
    lo_edit = QLineEdit(_fmt(None if chosen is None else chosen[0]))
    hi_edit = QLineEdit(_fmt(None if chosen is None else chosen[1]))
    for edit in (lo_edit, hi_edit):
        edit.setPlaceholderText("auto")
        edit.setFixedWidth(theme.Metrics.num_field_w)
    lay.addWidget(lo_edit)
    lay.addWidget(QLabel("max"))
    lay.addWidget(hi_edit)
    auto = QPushButton("Auto")
    auto.setProperty("variant", "secondary")
    auto.setToolTip("Back to each picture's own scale (the 1st to 99th percentile of its pixels).")
    lay.addWidget(auto)

    def commit() -> None:
        low, high = _number(lo_edit.text()), _number(hi_edit.text())
        if not lo_edit.text().strip() and not hi_edit.text().strip():
            nwc.set_display_range(ctx, None, None)
        elif low is not None and high is not None and high > low:
            nwc.set_display_range(ctx, low, high)

    def to_auto() -> None:
        lo_edit.clear()
        hi_edit.clear()
        nwc.set_display_range(ctx, None, None)

    lo_edit.editingFinished.connect(commit)
    hi_edit.editingFinished.connect(commit)
    auto.clicked.connect(lambda _=False: to_auto())
    ctx.widgets.register("_nw_range_lo_edit", lo_edit)
    ctx.widgets.register("_nw_range_hi_edit", hi_edit)
    ctx.widgets.register("_nw_range_auto_btn", auto)
    ctx.widgets.register("_nw_range_popover", pop)
    pop.setContentWidget(row)
    pop.popup(anchor, side="bottom", align="start")


def refresh_display_range_buttons(ctx) -> None:
    """Every Display range button naming the range in force for the page's image channel now."""
    text = button_text(ctx)
    for name in DISPLAY_RANGE_BUTTONS:
        button = ctx.widgets.get_or_none(name)
        if button is not None:
            button.setText(text)
