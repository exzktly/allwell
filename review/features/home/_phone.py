"""Home's Overview in phone layout: one column, no sideways scroll.

At the desk the Overview puts blocks side by side — the masthead's title beside
its run details and Reload, the next-step sentence beside its button, the plate
beside its legend and both beside the notes, Health beside Run parameters — and
lays the stat tiles in one row and the saved-plot thumbnails six to a row. A
phone held upright is about 400 px wide, and the page was over 800 px wide
before a single path or well list was drawn into it, so every block past the
first was reached by scrolling sideways.

In phone layout:

1. **Every side-by-side row stacks.** The rows are named :data:`SIDE_BY_SIDE`
   where they are built (``tab_view``) and turned top-to-bottom here, back to
   left-to-right at the desk. Named rather than found by measuring, because
   the request is "the page reads top to bottom", not "only the rows that
   happen to overflow at this width": a masthead that just fits beside its
   button on one phone does not on the next.
2. **The grids are one column** — the stat tiles and the thumbnails.
   :func:`grid_columns` is what the painters ask.
3. **Whatever is still too wide is fitted** by ``ui/phone_fit``, the pass the
   Datasets tools use: a health row's label, value and well list, a button
   label, a recent dataset's folder path (broken after its separators).

It runs when phone layout turns on or off, after every repaint of the page
(``refresh_home``), and when the page's width changes — the phone turning — via
:class:`HomeScroll`'s own ``resizeEvent``. An override rather than an event
filter: Qt calls a filter on the widgets it watches while they are being
destroyed, and this page rebuilds its rows on every repaint
(``widgets/removable_row.py`` has the crash that came from).
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QBoxLayout, QScrollArea

from core import phone_layout
from core.safe import swallow

#: Object name of every Overview row that sits side by side at the desk and
#: stacks in phone layout.
SIDE_BY_SIDE = "HomeSideBySide"

#: Where the fitted width is recorded on the scroll area, so a resize that does
#: not change the width (a height change, a repaint) does not re-fit.
_P_FITTED_AT = "_awHomeFittedAt"

#: The placeholder's own horizontal size policy, kept while phone layout
#: replaces it (:func:`fit_placeholder`).
_P_DESK_POLICY = "_awHomeDeskPolicy"


def grid_columns(desk_columns: int) -> int:
    """How many columns a Home grid gets: *desk_columns*, or one on a phone."""
    return 1 if phone_layout.is_on() else desk_columns


class HomeScroll(QScrollArea):
    """The Overview's scroll area. Re-fits the page when its width changes in
    phone layout — the phone turning — through its own ``resizeEvent``."""

    def resizeEvent(self, event) -> None:  # Qt naming
        super().resizeEvent(event)
        if phone_layout.is_on() and not phone_layout.is_switching():
            if self.property(_P_FITTED_AT) != self.viewport().width():
                fit_page(self)


def apply(ctx, *, repaint: bool = True) -> None:
    """Put the Overview in the form the current layout wants. Idempotent.

    *repaint* redraws the two grids, whose column count follows the layout;
    ``refresh_home`` has just painted them and passes ``False``.
    """
    area = ctx.widgets.get_or_none("_home_scroll")
    if area is None:
        return
    page = area.widget()
    if page is None:
        return
    want = QBoxLayout.TopToBottom if phone_layout.is_on() else QBoxLayout.LeftToRight
    for row in page.findChildren(QBoxLayout, SIDE_BY_SIDE):
        if row.direction() != want:
            row.setDirection(want)
    if repaint:
        from .controller import repaint_grids
        repaint_grids(ctx)
    fit_page(area)
    fit_placeholder(ctx)


def fit_page(area: QScrollArea) -> None:
    """Fit the page to the phone's width, or give it back its desk form.

    Always from the desk form: an earlier fit is undone first, so a page that
    was repainted or turned wider is fitted from scratch rather than stacking
    on top of the last pass.
    """
    from ui import phone_fit
    with swallow("home: fit the overview to the phone"):
        phone_fit.unfit(area)
        area.setProperty(_P_FITTED_AT, None)
        if not phone_layout.is_on() or area.viewport().width() <= 0:
            return
        phone_fit.fit(area)
        area.setProperty(_P_FITTED_AT, area.viewport().width())


def fit_placeholder(ctx) -> None:
    """Fit the no-dataset placeholder's recent-dataset rows to the phone.

    The placeholder and the page are the two pages of one stack, and a stack
    is as wide as its widest page *whether that page is showing or not*. Its
    rows are a dataset name over the folder holding it — measured, three of
    them held the stack 653 px wide, so a loaded dataset's Overview was laid
    out wider than the phone by a page nobody could see. In phone layout the
    placeholder stops counting towards the stack's width (an ``Ignored``
    horizontal policy) and its rows are fitted to the width the stack has.
    """
    from PySide6.QtWidgets import QSizePolicy

    from ui import phone_fit
    stack = ctx.widgets.get_or_none("_home_empty_stack")
    placeholder = getattr(stack, "_empty_placeholder", None) if stack else None
    if placeholder is None:
        return
    with swallow("home: fit the placeholder to the phone"):
        phone_fit.unfit_content(placeholder)
        policy = placeholder.sizePolicy()
        saved = placeholder.property(_P_DESK_POLICY)
        if not phone_layout.is_on():
            if saved is not None:
                policy.setHorizontalPolicy(QSizePolicy.Policy(saved))
                placeholder.setSizePolicy(policy)
                placeholder.setProperty(_P_DESK_POLICY, None)
            return
        if saved is None:
            placeholder.setProperty(_P_DESK_POLICY,
                                    int(policy.horizontalPolicy().value))
            policy.setHorizontalPolicy(QSizePolicy.Ignored)
            placeholder.setSizePolicy(policy)
        if not placeholder.isVisible():
            # A page the stack is not showing keeps whatever size it last had;
            # fitting measures positions inside it, so give it the stack's.
            placeholder.resize(stack.size())
        phone_fit.fit_content(placeholder, stack.width())


def watch_layout(ctx, area: QScrollArea) -> None:
    """Re-apply when phone layout turns on or off, for as long as *area* lives.

    Deferred to the next event-loop turn: the listener is called from
    ``phone_layout.set_on``, before the shell has restyled the window to the
    phone's type scale, and fitting before the fonts change measures the desk's
    text. The timer is bound to *area*, so it never fires on a page that went.
    """
    def _on_layout(_on: bool) -> None:
        try:
            QTimer.singleShot(0, area, lambda: apply(ctx))
        except RuntimeError:
            phone_layout.remove_listener(_on_layout)  # the page went first

    phone_layout.add_listener(_on_layout)
    area.destroyed.connect(lambda *_: phone_layout.remove_listener(_on_layout))
