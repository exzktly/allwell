"""The Sample Map tab in phone layout: one column that scrolls by a drag.

At the desk the tab is the layer switch, the role strip, and a stack holding
the three layers, each laid out for a pane several hundred pixels wider than a
phone: Replicates puts its plate above a row of three labelled fields and a
button (measured 1,030 px of minimum width at the phone's type scale), Variables
sets its definition column beside its plate (774 px with a variable defined),
and Well Labels puts its help sentence beside Add Prefix and Add Suffix above a
twelve-column grid (761 px). Nothing above the stack scrolls, so on a 432 px
phone every layer was squeezed below its own minimum — the plate's labels ran
together, Quick Replicates' fields were cut to "Group R" and "Replica" — and on
a phone held sideways the stack was left 195 px of height for all of it.

In phone layout:

1. **The layer scrolls, the switch does not.** The strip, the grouping note and
   the stack (one widget, ``_sample_map_body``) move into a scroll area
   (:class:`SampleMapScroll`) that takes their place under the layer switch, so
   the switch stays in reach and everything under it scrolls by a drag like
   every other scroll area in phone layout (``PhoneShell._sweep`` grabs it).
   Leaving phone layout puts the body back where it was and hides the area: the
   desk's tab is the widgets it always was, in the layout it always had.
2. **A layer that is not showing does not size the stack.** A
   ``QStackedWidget`` is as wide and as tall as its largest page whether that
   page is showing or not — its ``heightForWidth`` takes the tallest page's,
   whatever that page's size policy says — so in a scroll area the stacked
   Variables layer held Well Labels' grid 1,955 px tall, every row of cells
   stretched to share it. Each page is a :class:`LayerPage`, which asks for no
   room while another layer is showing in phone layout, and is a plain
   ``QWidget`` at the desk.
3. **Each layer takes a phone form of its own** — a column, its fields under one
   another with a label column, its buttons full width. A layer's form is its
   own package's business (it knows which of its rows mean what), so each
   names its form in :data:`LAYER_PHONE_FORMS` the way it names its builder in
   ``controller.LAYER_BUILDERS``, and this module calls it. The generic pass in
   ``ui/phone_fit`` was tried alone first and made Replicates worse: it turns
   rows top-to-bottom by measuring, and a page that cannot grow taller then
   overlaps the rows it stacked.
4. **Whatever is still too wide is fitted** by ``ui/phone_fit``, the pass the
   Datasets tools and Home's Overview use, against the scroll area's viewport.

It runs when phone layout turns on or off (deferred one turn, so it measures the
phone's type scale rather than the desk's), when the layer changes, and when the
viewport's width changes — the phone turning, or the vertical scroll bar coming
and going as a layer grows or shrinks.
"""

from __future__ import annotations

import importlib

from PySide6.QtCore import QEvent, QSize, QTimer
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

from core import phone_layout
from core.safe import swallow
from ui import theme

#: Registry key of the widget that holds the strip, the note and the stack —
#: the part of the tab that scrolls in phone layout.
BODY_KEY = "_sample_map_body"
#: Registry key of the scroll area that holds it in phone layout.
SCROLL_KEY = "_sample_map_scroll"

#: The page's side gutters in phone layout, in phone points. At the desk the
#: layers inset only their left edge, for the rail's collapse toggle; in phone
#: layout the toggle is hidden and the page runs to both edges of the screen, so
#: text started at x = 0 and the layers' buttons ran into the right edge.
GUTTER_PT = 12

#: ``layer -> (module, function)`` for each layer's phone form. Each function
#: takes ``(ctx, on)``, puts its layer in the phone form when *on* and back in
#: the desk form otherwise, and is idempotent. Resolved by ``importlib`` for the
#: same reason the builders are (``controller.LAYER_BUILDERS``).
LAYER_PHONE_FORMS: dict[str, tuple[str, str]] = {
    "Replicates": ("review.features.home.groups.tab_view",
                   "set_replicates_phone_form"),
    "Variables": ("review.features.home.variables.tab_view",
                  "set_variables_phone_form"),
    "Well Labels": ("review.features.home.labels.tab_view",
                    "set_labels_phone_form"),
}

#: The width the body was last fitted at, so a resize that keeps it (a height
#: change) does not re-fit.
_P_FITTED_AT = "_awSampleMapFittedAt"
#: The body's own ``autoFillBackground``: ``QScrollArea.setWidget`` turns it on.
_P_DESK_FILL = "_awSampleMapDeskFill"


def gutter_px() -> int:
    """:data:`GUTTER_PT` in pixels at the current touch unit (0 at the desk)."""
    unit = theme.touch_unit()
    return round(GUTTER_PT * unit) if unit else 0


class LayerPage(QWidget):
    """One layer's page of the stack; see the module docstring, 2.

    The override sits on the page rather than on the stack because the stack's
    size is not asked of the ``QStackedWidget``: a layout reads it from the
    widget's own ``QStackedLayout``, which asks each *page* — for its minimum
    and size hints, and for its ``heightForWidth`` whatever its size policy.
    A page that is not the one showing answers "nothing" while phone layout is
    on, and what a ``QWidget`` answers otherwise.
    """

    def _stands_aside(self) -> bool:
        if not phone_layout.is_on():
            return False
        stack = self.parentWidget()
        current = getattr(stack, "currentWidget", None)
        return callable(current) and current() is not self

    def sizeHint(self):  # Qt naming
        return QSize(0, 0) if self._stands_aside() else super().sizeHint()

    def minimumSizeHint(self):  # Qt naming
        return QSize(0, 0) if self._stands_aside() else super().minimumSizeHint()

    def heightForWidth(self, width: int) -> int:  # Qt naming
        return 0 if self._stands_aside() else super().heightForWidth(width)


class SampleMapScroll(QScrollArea):
    """The scroll area the tab's body moves into in phone layout.

    Re-fits the body when its viewport changes width — the phone turning, or
    the vertical scroll bar appearing once a layer is taller than the screen,
    which narrows the viewport by the bar's width without resizing the area —
    and when the body has grown wider than the viewport at the same width. A
    layer repaints rows the fit never saw (*+ Variable* swaps the empty state
    for the plate, the chips and the fill sentence, measured 590 px of a 414 px
    viewport), and the horizontal scroll bar that then appears resizes the
    viewport, which is the moment to fit again.
    The viewport's own resize is read in ``viewportEvent`` rather than through
    an event filter, and the fit is deferred to the next turn of the event loop:
    fitting changes the body's minimum height, which can bring the scroll bar
    on, which resizes the viewport again, and that must not recurse.
    """

    def __init__(self, ctx, parent: QWidget) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._pending = False
        self.setObjectName("SampleMapScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)

    def viewportEvent(self, event) -> bool:  # Qt naming
        if event.type() == QEvent.Resize and phone_layout.is_on() \
                and not phone_layout.is_switching():
            body = self.widget()
            width = self.viewport().width()
            if body is not None and (body.property(_P_FITTED_AT) != width
                                     or body.minimumSizeHint().width() > width):
                self.schedule_fit()
        return super().viewportEvent(event)

    def schedule_fit(self) -> None:
        """Fit on the next turn of the event loop, once however often asked."""
        if self._pending:
            return
        self._pending = True

        def _run() -> None:
            self._pending = False
            fit(self._ctx)

        QTimer.singleShot(0, self, _run)


def install(ctx, frame: QWidget, body: QWidget) -> SampleMapScroll:
    """Build the (hidden) scroll area beside *body* and follow phone layout."""
    outer = frame.layout()
    scroll = ctx.widgets.register(SCROLL_KEY, SampleMapScroll(ctx, frame))
    scroll.hide()
    outer.insertWidget(outer.indexOf(body) + 1, scroll, 1)

    def _on_layout(_on: bool) -> None:
        try:
            QTimer.singleShot(0, scroll, lambda: apply(ctx))
        except RuntimeError:
            phone_layout.remove_listener(_on_layout)  # the tab went first

    phone_layout.add_listener(_on_layout)
    scroll.destroyed.connect(lambda *_: phone_layout.remove_listener(_on_layout))
    if phone_layout.is_on():
        apply(ctx)
    return scroll


def apply(ctx) -> None:
    """Put the tab in the form the current layout wants. Idempotent."""
    body = ctx.widgets.get_or_none(BODY_KEY)
    scroll = ctx.widgets.get_or_none(SCROLL_KEY)
    if body is None or scroll is None:
        return
    on = phone_layout.is_on()
    with swallow("sample map: moving the body for phone layout"):
        _place_body(body, scroll, on)
    gutter = gutter_px() if on else 0
    body.layout().setContentsMargins(gutter, 0, gutter, gutter)
    for layer, (module, name) in LAYER_PHONE_FORMS.items():
        with swallow(f"sample map: the {layer} layer's phone form"):
            getattr(importlib.import_module(module), name)(ctx, on)
    _resize_stack(ctx)
    fit(ctx)


def _place_body(body: QWidget, scroll: SampleMapScroll, on: bool) -> None:
    if on:
        if scroll.widget() is body:
            return
        if body.property(_P_DESK_FILL) is None:
            body.setProperty(_P_DESK_FILL, bool(body.autoFillBackground()))
        scroll.setWidget(body)
        scroll.show()
        return
    if scroll.widget() is not body:
        return
    # Hidden first: for a moment the body and the area holding it would both
    # count towards the tab's minimum height, and a window grows to that.
    scroll.hide()
    scroll.takeWidget()
    outer = scroll.parentWidget().layout()
    outer.insertWidget(outer.indexOf(scroll), body, 1)
    fill = body.property(_P_DESK_FILL)
    if fill is not None:
        body.setAutoFillBackground(bool(fill))
        body.setProperty(_P_DESK_FILL, None)
    body.show()


def _resize_stack(ctx) -> None:
    """Have the stack ask its pages again. Each page's hints are cached by the
    stack's layout (``QWidgetItemV2``) until that page reports a change, so a
    page that stopped or started standing aside has to say so itself."""
    from .controller import STACK_KEY

    stack = ctx.widgets.get_or_none(STACK_KEY)
    if stack is None:
        return
    for i in range(stack.count()):
        stack.widget(i).updateGeometry()
    stack.updateGeometry()


def fit(ctx) -> None:
    """Fit the body to the viewport's width, or give it its desk form back.

    Always from the unfitted form: an earlier fit is undone first, so a layer
    that was repainted or turned wider is fitted from scratch.
    """
    from ui import phone_fit

    body = ctx.widgets.get_or_none(BODY_KEY)
    scroll = ctx.widgets.get_or_none(SCROLL_KEY)
    if body is None or scroll is None:
        return
    with swallow("sample map: fitting the layer to the phone"):
        phone_fit.unfit_content(body)
        body.setProperty(_P_FITTED_AT, None)
        if not phone_layout.is_on() or scroll.widget() is not body:
            return
        width = scroll.viewport().width()
        if width <= 0:
            return
        phone_fit.fit(scroll)
        body.setProperty(_P_FITTED_AT, scroll.viewport().width())


def on_layer_shown(ctx) -> None:
    """A layer was switched to: let it alone size the stack, and fit it."""
    if not phone_layout.is_on():
        return
    _resize_stack(ctx)
    scroll = ctx.widgets.get_or_none(SCROLL_KEY)
    if scroll is not None:
        scroll.verticalScrollBar().setValue(0)
        scroll.schedule_fit()
