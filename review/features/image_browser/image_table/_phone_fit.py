"""The Image Table in phone layout: cells as wide as the screen allows.

At the desk a cell is 240 px times the zoom — a size chosen for a monitor, where
three or four columns leave room to spare. On a phone over remote desktop the
same 240 px left a portrait screen a third empty and a landscape one two thirds
empty, while the page still scrolled sideways for the builder above it. In phone
layout the grid instead divides the width it has between its columns, and the
zoom multiplies *that*: 100 % is "the columns fill the screen", and zooming in
past it scrolls sideways the way zooming in on a phone does.

The fitted size depends on the screen, so the grid is drawn again when phone
layout turns on or off and when the phone turns (a debounced resize of the page's
viewport), from the decode cache — no image is read twice.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QTimer

from core import phone_layout
from core.safe import swallow

#: The smallest a fitted cell may be, in pixels: below this a field of view is
#: a smudge, and the grid should scroll rather than shrink further.
MIN_CELL_PX = 80

#: Debounce for a refit after a resize: the remote desktop resizes in steps
#: when the phone turns, and one redraw at the end is what is wanted.
REFIT_DELAY_MS = 250

#: A resize narrower than this share of the width is not a new screen (a
#: scrollbar appearing, a dock settling) and does not redraw the grid.
REFIT_MIN_CHANGE = 0.05


def fit_cell_px(avail_w: int, n_cols: int, *, label_w: int, per_col_px: int,
                spacer_w: int, zoom: float) -> int:
    """The cell size that lays *n_cols* columns across *avail_w* pixels.

    *label_w* is the width the row labels take, *per_col_px* what each column
    spends beyond its image (the grid's spacing and the cell's own margins),
    *spacer_w* the gaps between facet groups. *zoom* multiplies the fitted size,
    so 1.0 fills the width exactly. Never below :data:`MIN_CELL_PX`.
    """
    n = max(1, int(n_cols))
    room = avail_w - label_w - spacer_w - n * per_col_px
    return max(MIN_CELL_PX, int(room / n * float(zoom or 1.0)))


def _label_width(plan, ui_scale: float) -> int:
    """The width the row-label columns of *plan* take on screen, measured with
    the fonts ``facet_view`` draws them in."""
    from PySide6.QtGui import QFont, QFontMetrics

    def metrics(px: int) -> QFontMetrics:
        f = QFont()
        f.setPixelSize(max(1, round(px * ui_scale)))
        return QFontMetrics(f)

    inner, group = metrics(plan.label_pt), metrics(plan.group_pt)
    widths: dict[int, int] = {}
    for text, _gr, gc, _rs, _cs, is_group in plan.left:
        if not text:
            continue
        # A group label is drawn vertically (``facet_view._VLabel``), so its
        # width is a line height plus the band's padding.
        w = group.height() + 16 if is_group else inner.horizontalAdvance(text) + 8
        widths[gc] = max(widths.get(gc, 0), w)
    # The corner ("T:0h · FOV:F001", the values held fixed) sits over the label
    # columns, so they are at least as wide as it is.
    corner = 0
    if plan.corner:
        corner = metrics(max(8, plan.label_pt - 1)).horizontalAdvance(plan.corner) + 8
    return max(sum(widths.values()), corner)


def phone_cell_px(ctx, cells, plan, zoom: float, grid) -> int | None:
    """The fitted cell size for the grid in phone layout, or ``None`` at the
    desk (and wherever the page's width cannot be read), which keeps the desk's
    240 px rule."""
    if not phone_layout.is_on() or not cells or not cells[0]:
        return None
    from PySide6.QtWidgets import QScrollArea

    from ui import theme
    host = grid.parentWidget()
    scroll = host
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    if scroll is None:
        return None
    inner = scroll.widget()
    margins = inner.layout().contentsMargins() if inner and inner.layout() else None
    avail = scroll.viewport().width() - (
        margins.left() + margins.right() if margins is not None else 0)
    # The grid this sizes is usually what makes the page tall enough to need a
    # vertical scrollbar, which then takes its width out of the viewport — a
    # change too small to trigger a refit, left as a sideways scroll of exactly
    # that width. So its width is reserved before it appears.
    bar = scroll.verticalScrollBar()
    if not bar.isVisible():
        avail -= bar.sizeHint().width()
    label_w = _label_width(plan, theme.ui_scale()) if plan is not None else 0
    spacer_w = (len(plan.spacer_cols) * plan.col_gap) if plan is not None else 0
    # Each cell widget has 2 px margins either side (controller._render_cell's
    # host), and the grid spends its spacing once per column.
    per_col = max(0, grid.spacing()) + 4
    return fit_cell_px(avail, len(cells[0]), label_w=label_w, per_col_px=per_col,
                       spacer_w=spacer_w, zoom=zoom)


class _RefitOnResize(QObject):
    """Restart the refit timer when the page's viewport changes width by more
    than :data:`REFIT_MIN_CHANGE` while phone layout is on — the phone turned."""

    def __init__(self, parent: QObject, timer: QTimer, fitted: dict) -> None:
        super().__init__(parent)
        self._timer = timer
        self._fitted = fitted

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if ev.type() == QEvent.Resize and phone_layout.is_on():
            old = self._fitted["width"] or ev.oldSize().width()
            new = ev.size().width()
            if old > 0 and abs(new - old) / old > REFIT_MIN_CHANGE:
                self._timer.start()
        return False


def install_refit(ctx, scroll, render_host, regenerate: Callable[[object], None],
                  relabel: Callable[[object], None] | None = None) -> None:
    """Redraw a rendered grid (``regenerate(ctx)``, the tab's Generate) when
    phone layout turns on or off, and when the page's width changes while it
    is on (the phone turned). *relabel(ctx)* runs at once on a layout change,
    for text that names where the controls are.

    The listener is removed with *render_host*, so a torn-down tab is never
    called back.
    """
    timer = QTimer(render_host)
    timer.setSingleShot(True)
    timer.setInterval(REFIT_DELAY_MS)
    fitted = {"width": None}

    def _refit() -> None:
        # Never inside a switch: its restyle lets timers fire, and a redraw
        # there deleted and rebuilt the grid while the restyle was walking the
        # window's widgets. Asked again once, when the switch ends — never by
        # re-arming the timer from inside it, which can hold an event loop.
        if phone_layout.is_switching():
            phone_layout.after_switch(timer.start)
            return
        # Only a grid that has been drawn is redrawn: an empty tab stays empty
        # until its user presses Generate.
        if not getattr(ctx.state.image_table, "last_render", None):
            return
        with swallow("image table: redraw for the screen"):
            regenerate(ctx)
        fitted["width"] = scroll.viewport().width()

    timer.timeout.connect(_refit)

    def _on_layout(_on: bool) -> None:
        try:
            timer.start()
        except RuntimeError:
            phone_layout.remove_listener(_on_layout)  # the tab went first
            return
        if relabel is not None:
            with swallow("image table: relabel for the layout"):
                relabel(ctx)

    phone_layout.add_listener(_on_layout)
    render_host.destroyed.connect(lambda *_: phone_layout.remove_listener(_on_layout))
    scroll.viewport().installEventFilter(_RefitOnResize(render_host, timer, fitted))
